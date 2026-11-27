"""The join of item labels (REPORT 6.5.1) for what a reduction covers.

Labels only rise: integrity is the worst of the items', confidentiality the
union of their sources read by the readers every item allows. An item with no
confidentiality claim adds none, so the join keeps every claim any item made
(for every item i, ``join ⊒ label(i)``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, cast

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.labels import Labels

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import JsonValue

    from harness_core.journal.types import JournalEvent

Integrity = Literal["trusted", "internal", "external"]
_ORDER: dict[str, int] = {"trusted": 0, "internal": 1, "external": 2}
# Readers no principal is in: two items readable by disjoint principal sets.
NO_READER = "p_nobody"
_BREADTH: dict[str, int] = {"public": 2, "org": 1}

Readers = str | frozenset[str]


def _readers(value: JsonValue) -> Readers:
    if isinstance(value, list):
        return frozenset(str(v) for v in value)
    return str(value)


def _meet(a: Readers, b: Readers) -> Readers:
    if isinstance(a, str) and isinstance(b, str):
        return a if _BREADTH.get(a, 0) <= _BREADTH.get(b, 0) else b
    if isinstance(a, str):
        return b
    if isinstance(b, str):
        return a
    return (a & b) or frozenset({NO_READER})


def join_item_labels(labels: Iterable[Labels | None]) -> Labels | None:
    """``None`` when no item carried a label (nothing to claim)."""
    integrity: str | None = None
    readers: Readers | None = None
    sources: set[str] = set()
    for label in labels:
        if label is None:
            continue
        integrity = label.integrity if integrity is None or _ORDER[label.integrity] > _ORDER[integrity] else integrity
        conf = label.confidentiality
        if conf is None:
            continue
        dumped = cast("dict[str, JsonValue]", conf.model_dump(mode="json", exclude_none=True))
        mine = _readers(dumped["readers"])
        readers = mine if readers is None else _meet(readers, mine)
        sources.update(str(s) for s in cast("list[JsonValue]", dumped.get("sources") or []))
    if integrity is None:
        return None
    doc: dict[str, JsonValue] = {"integrity": integrity}
    if readers is not None:
        who: JsonValue = cast("JsonValue", sorted(readers)) if isinstance(readers, frozenset) else readers
        conf_doc: dict[str, JsonValue] = {"readers": who}
        if sources:
            conf_doc["sources"] = cast("JsonValue", sorted(sources))
        doc["confidentiality"] = conf_doc
    return validate_contract(Labels, doc)


def labels_of(value: JsonValue | None) -> Labels | None:
    if not isinstance(value, dict):
        return None
    return validate_contract(Labels, value)


def event_labels(event: JournalEvent) -> list[Labels]:
    """What an event says reached the context: its envelope labels and a tool outcome's item labels."""
    found = [labels_of(cast("JsonValue", event.labels))]
    if event.type == "tool.outcome":
        found.append(labels_of(event.data.get("labels")))
    return [label for label in found if label is not None]


def range_labels(events: Iterable[JournalEvent], from_seq: int, to_seq: int) -> Labels | None:
    """The join of every label an event of ``[from_seq, to_seq]`` carries."""
    return join_item_labels(
        label for event in events if from_seq <= event.seq <= to_seq for label in event_labels(event)
    )


def labels_json(label: Labels | None) -> dict[str, JsonValue] | None:
    if label is None:
        return None
    return cast("dict[str, JsonValue]", label.model_dump(mode="json", by_alias=True, exclude_none=True))


__all__ = [
    "NO_READER",
    "event_labels",
    "join_item_labels",
    "labels_json",
    "labels_of",
    "range_labels",
]
