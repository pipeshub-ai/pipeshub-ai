"""A span a recognizer found, before it is grounded and typed."""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import ExtractorName, Grounding


@dataclass
class RawMention:
    kind: EntityKind
    surface: str
    block_index: int
    block_id: str
    char_start: int
    char_end: int
    extractor: ExtractorName
    normalized_hint: str = ""
    evidence_score: float = 1.0
    grounding: Grounding = "exact"
