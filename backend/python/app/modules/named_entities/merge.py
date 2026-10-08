"""Resolve overlapping spans and collapse one record's mentions onto entities."""

from __future__ import annotations

from bisect import bisect_left, insort

from app.modules.named_entities.domain.kinds import EntityKind, EntitySource, spec_for
from app.modules.named_entities.domain.models import Mention, NamedEntity
from app.modules.named_entities.domain.values import ContactValue
from app.modules.named_entities.mentions import RawMention
from app.modules.named_entities.normalizers import normalize_value
from app.modules.named_entities.normalizers.dates import NormalizationContext
from app.modules.named_entities.normalizers.names import canonical_name, norm_key_for
from app.modules.named_entities.recognizers.pattern import is_suppressed_secret

_PRIORITY = {"pattern": 0, "value": 1, "agent": 2, "single_call": 3}


def resolve_overlaps(mentions: list[RawMention]) -> list[RawMention]:
    """Pattern beats value beats the model. A longer span wins inside one source."""
    ordered = sorted(
        mentions,
        key=lambda item: (
            _PRIORITY.get(item.extractor, 9),
            -(item.char_end - item.char_start),
            item.char_start,
        ),
    )
    # Kept spans of one block are disjoint and sorted, so only the neighbours of a
    # new span's position can overlap it.
    spans: dict[int, list[tuple[int, int]]] = {}
    kept: list[RawMention] = []
    for mention in ordered:
        block = spans.setdefault(mention.block_index, [])
        span = (mention.char_start, mention.char_end)
        at = bisect_left(block, span)
        if any(_overlaps(span, block[i]) for i in (at - 1, at) if 0 <= i < len(block)):
            continue
        insort(block, span)
        kept.append(mention)
    return kept


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


# Longer spans are not entities (a data URI, a pasted blob), and stay out of keys.
MAX_SURFACE_CHARS = 2048


def mentions_to_entities(
    mentions: list[RawMention], ctx: NormalizationContext
) -> list[NamedEntity]:
    grouped: dict[tuple[EntityKind, str], NamedEntity] = {}
    for raw in resolve_overlaps(mentions):
        if len(raw.surface) > MAX_SURFACE_CHARS or is_suppressed_secret(raw.surface):
            continue
        spec = spec_for(raw.kind)
        if spec is None:
            continue
        value = normalize_value(raw.kind, raw.surface, ctx)
        if value is None and spec.source is EntitySource.VALUE:
            # A date or amount whose text states none is not stored under a name key.
            continue
        display = canonical_name(raw.surface, organization=raw.kind is EntityKind.ORGANIZATION)
        if raw.kind is EntityKind.URL and isinstance(value, ContactValue):
            # The graph shows the canonical link, never the credentials a raw one can carry.
            display = value.canonical
        key = norm_key_for(raw.kind, display, value)
        entity = grouped.get((raw.kind, key))
        mention = Mention(
            block_index=raw.block_index,
            block_id=raw.block_id,
            char_start=raw.char_start,
            char_end=raw.char_end,
            # Whole, so the block text at the mention's offsets is its surface.
            surface=raw.surface,
            grounding=raw.grounding,
            extractor=raw.extractor,
            evidence_score=raw.evidence_score,
        )
        if entity is None:
            grouped[(raw.kind, key)] = NamedEntity(
                kind=raw.kind,
                tags=list(spec.tags),
                display_name=display[:256] or raw.surface[:256],
                norm_key=key,
                value=value,
                normalized_raw="" if is_suppressed_secret(raw.normalized_hint) else raw.normalized_hint[:256],
                mentions=[mention],
            )
        else:
            entity.mentions.append(mention)
    return list(grouped.values())
