"""Caps applied after extraction. Extra items are dropped and counted."""

from __future__ import annotations

from app.modules.named_entities.domain.config import NamedEntityBudgets
from app.modules.named_entities.domain.kinds import EntityKind
from app.modules.named_entities.domain.models import NamedEntity


def apply_caps(
    entities: list[NamedEntity],
    budgets: NamedEntityBudgets,
    enabled: frozenset[EntityKind],
) -> tuple[list[NamedEntity], int]:
    dropped = 0
    kept: list[NamedEntity] = []
    per_block: dict[int, int] = {}
    for entity in entities:
        if entity.kind not in enabled:
            dropped += 1
            continue
        mentions = []
        for mention in entity.mentions:
            count = per_block.get(mention.block_index, 0)
            if count >= budgets.max_per_block:
                dropped += 1
                continue
            if len(mentions) >= budgets.max_mentions_per_entity:
                dropped += 1
                continue
            per_block[mention.block_index] = count + 1
            mentions.append(mention)
        if not mentions:
            continue
        if len(kept) >= budgets.max_entities:
            dropped += 1
            continue
        entity.mentions = mentions
        entity.display_name = entity.display_name[: budgets.max_text_chars]
        kept.append(entity)
    return kept, dropped
