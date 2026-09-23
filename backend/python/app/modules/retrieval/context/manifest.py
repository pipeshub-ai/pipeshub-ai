"""Which characters of a rendered knowledge result show which blocks.

Built while rendering, never by parsing the text back: a document can itself
contain lines that look like ``[3|ref7]``. The budget uses it to measure what
each unit costs, and a later pass uses it to find a block's other copies.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
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
    unit_position: int
    """Position of the unit in the list that was rendered."""


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
    shown_blocks: frozenset[BlockKey] = frozenset()
    """Blocks shown without spans: a fetch result is never edited, so only
    what it covers matters."""

    @property
    def blocks(self) -> frozenset[BlockKey]:
        return self.shown_blocks.union(key for segment in self.segments for key in segment.blocks)

    def shifted(self, offset: int) -> ContentManifest:
        """The same manifest for text that has ``offset`` characters in front."""
        return replace(
            self,
            segments=tuple(
                replace(s, start=s.start + offset, end=s.end + offset) for s in self.segments
            ),
            records=tuple(
                replace(r, start=r.start + offset, end=r.end + offset) for r in self.records
            ),
        )


_REGISTRY_KEY = "content_manifests"


@dataclass
class ManifestRegistry:
    """The manifests of one request's knowledge results.

    Keyed by a digest of the exact text a tool returned. A message any later
    step changed (cleared, truncated, compacted) no longer matches, so it is
    treated as not showing anything: a duplicate is kept rather than lost.
    Prefetch goes into the system prompt, which is rebuilt every call and
    never shaped, so it always counts as shown.
    """

    _by_digest: dict[str, ContentManifest] = field(default_factory=dict)
    prefetch: list[ContentManifest] = field(default_factory=list)

    def register(self, text: str, manifest: ContentManifest) -> None:
        self._by_digest[_digest(text)] = manifest

    def lookup(self, text: str) -> ContentManifest | None:
        return self._by_digest.get(_digest(text))

    def register_prefetch(self, manifest: ContentManifest) -> None:
        self.prefetch.append(manifest)


def manifest_registry(tool_state: dict[str, Any]) -> ManifestRegistry:
    """The request's registry, created on first use."""
    registry = tool_state.get(_REGISTRY_KEY)
    if not isinstance(registry, ManifestRegistry):
        registry = tool_state[_REGISTRY_KEY] = ManifestRegistry()
    return registry


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
            unit_position=position,
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
