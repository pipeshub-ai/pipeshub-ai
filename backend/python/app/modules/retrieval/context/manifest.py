"""Which characters of a rendered knowledge result show which blocks.

Built while rendering, never by parsing the text back: a document can itself
contain lines that look like ``[3|ref7]``. The budget uses it to measure what
each unit costs, and a later pass uses it to find a block's other copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.models.blocks import BlockType
from app.modules.retrieval.context.units import unit_block_indices, unit_children

if TYPE_CHECKING:
    from app.modules.retrieval.context.units import Unit


@dataclass(frozen=True)
class BlockKey:
    virtual_record_id: str
    block_index: int


class ManifestSource(StrEnum):
    SEARCH = "search"
    FETCH = "fetch"
    PREFETCH = "prefetch"


@dataclass(frozen=True)
class Segment:
    """One unit's text: ``text[start:end]``."""

    start: int
    end: int
    blocks: frozenset[BlockKey]
    elidable: bool
    """Whether another copy of ``blocks`` can stand in for this one."""


@dataclass(frozen=True)
class RecordSpan:
    """One ``<record>…</record>`` section: ``text[start:end]``."""

    virtual_record_id: str
    record_id: str
    start: int
    end: int


@dataclass(frozen=True)
class ContentManifest:
    source: ManifestSource
    segments: tuple[Segment, ...]
    records: tuple[RecordSpan, ...]

    @property
    def blocks(self) -> frozenset[BlockKey]:
        return frozenset(key for segment in self.segments for key in segment.blocks)


def build_manifest(
    source: ManifestSource,
    records_content: list[list[dict[str, Any]]],
    units: list[Unit],
    item_units: dict[int, int],
    virtual_record_id_to_result: dict[str, Any],
    *,
    separator: str = "\n",
) -> ContentManifest:
    """Spans for text made by joining each record's text items, records joined by ``separator``.

    ``item_units`` maps ``id(item)`` to the position in ``units`` it renders,
    as ``build_message_content_array`` fills it. Items that are not text
    (images) take no characters.
    """
    unit_spans: dict[int, list[int]] = {}
    record_spans: list[RecordSpan] = []
    offset = 0
    for record_index, record_content in enumerate(records_content):
        if record_index:
            offset += len(separator)
        record_start = offset
        record_vrid = ""
        for item in record_content:
            length = len(item["text"]) if item.get("type") == "text" else 0
            position = item_units.get(id(item))
            if position is not None:
                span = unit_spans.setdefault(position, [offset, offset])
                span[1] = offset + length
                record_vrid = record_vrid or str(units[position].get("virtual_record_id") or "")
            offset += length
        record = virtual_record_id_to_result.get(record_vrid) or {}
        record_spans.append(RecordSpan(
            virtual_record_id=record_vrid,
            record_id=str(record.get("id") or ""),
            start=record_start,
            end=offset,
        ))

    segments = tuple(
        Segment(
            start=start,
            end=end,
            blocks=_block_keys(units[position]),
            elidable=_elidable(units[position]),
        )
        for position, (start, end) in sorted(unit_spans.items(), key=lambda item: item[1][0])
    )
    return ContentManifest(source=source, segments=segments, records=tuple(record_spans))


def _block_keys(unit: Unit) -> frozenset[BlockKey]:
    vrid = str(unit.get("virtual_record_id") or "")
    if not vrid:
        return frozenset()
    return frozenset(BlockKey(vrid, index) for index in unit_block_indices(unit))


def _elidable(unit: Unit) -> bool:
    """A record summary and anything carrying an image have no stand-in.

    Images are admitted once per request by content hash, so another copy
    of the block may be only a text marker; a summary is never rendered by
    fetch at all.
    """
    if unit.get("block_type") in (BlockType.IMAGE.value, BlockType.RECORD_SUMMARY.value):
        return False
    if unit.get("block_index") is None:
        return False
    return not any(child.get("block_type") == BlockType.IMAGE.value for child in unit_children(unit))
