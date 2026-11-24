"""Announce how one record's named-entity extraction run ended."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app import platform_events

if TYPE_CHECKING:
    from app.modules.named_entities.domain.models import NamedEntityExtraction


def announce_run(extraction: NamedEntityExtraction, *, outcome: str, seconds: float) -> None:
    """Say how one record's extraction run ended: counts and timings, never a surface form or a value."""
    if not platform_events.has_listeners("named_entity.run.persisted"):
        return  # the tallies below walk every entity and mention
    entities_by_kind: dict[str, int] = {}
    mentions_by_grounding: dict[str, int] = {}
    if outcome == "written":
        for entity in extraction.entities:
            entities_by_kind[entity.kind.value] = entities_by_kind.get(entity.kind.value, 0) + 1
            for mention in entity.mentions:
                grounding = mention.grounding or "none"
                mentions_by_grounding[grounding] = mentions_by_grounding.get(grounding, 0) + 1
    stats = extraction.stats
    platform_events.emit(
        "named_entity.run.persisted",
        strategy=extraction.strategy,
        termination_reason=extraction.termination_reason,
        outcome=outcome,
        duration_seconds=seconds,
        entities_by_kind=entities_by_kind,
        mentions_by_grounding=mentions_by_grounding,
        dropped_cap=stats.dropped_cap,
        rejected=stats.rejected,
        turns=stats.turns,
        tool_calls=stats.tool_calls,
        tokens_in=stats.tokens_in,
        tokens_out=stats.tokens_out,
    )
