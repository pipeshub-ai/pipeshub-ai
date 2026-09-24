"""Rendering ordered units as the ``<record>`` text the model reads."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.context.ranking import RelevanceRanker
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    build_message_content_array,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.modules.retrieval.context.units import Unit
    from app.utils.chat_helpers import RecordIdShortener
    from app.utils.image_admission import ImageAdmission


@dataclass
class RenderedKnowledge:
    records: list[str]
    """One rendered ``<record>`` per record, most relevant first."""
    units: list[Unit]
    """The units that made it into ``records``; what the model can cite."""
    images: list[dict[str, Any]] = field(default_factory=list)
    """Images collected for multimodal delivery alongside the text."""
    omitted_records: int = 0
    """Records left out entirely to stay within ``max_chars``."""

    @property
    def text(self) -> str:
        return "\n".join(self.records)


def render_knowledge(
    units: list[Unit],
    virtual_record_id_to_result: dict[str, Any],
    *,
    ref_mapper: CitationRefMapper,
    is_multimodal_llm: bool,
    record_id_shortener: RecordIdShortener | None = None,
    image_budget: ImageBudget | None = None,
    image_admission: ImageAdmission | None = None,
    max_chars: int | None = None,
) -> RenderedKnowledge:
    """Render ``units`` (already in reading order) record by record.

    With ``max_chars`` a record that does not fit keeps only its most relevant
    units, and one with none that fit is left out, so an oversized result
    loses its least relevant content rather than whatever a later character
    cut happens to land on. The most relevant unit is always kept. Records are measured on a throwaway render first so that
    citation refs and images are only ever assigned to what is shown.
    """
    units = [
        unit for unit in units
        if virtual_record_id_to_result.get(unit.get("virtual_record_id")) is not None
    ]
    if max_chars is not None:
        units, omitted = _fit_to_budget(
            units, virtual_record_id_to_result, ref_mapper,
            is_multimodal_llm=is_multimodal_llm,
            record_id_shortener=record_id_shortener,
            max_chars=max_chars,
        )
    else:
        omitted = 0

    images: list[dict[str, Any]] = []
    content_array, _ = build_message_content_array(
        units,
        virtual_record_id_to_result,
        is_multimodal_llm=is_multimodal_llm,
        ref_mapper=ref_mapper,
        from_tool=True,
        record_id_shortener=record_id_shortener,
        collected_images=images,
        image_budget=image_budget,
        image_admission=image_admission,
    )
    return RenderedKnowledge(
        records=[_record_text(record) for record in content_array],
        units=units,
        images=images,
        omitted_records=omitted,
    )


def _fit_to_budget(
    units: list[Unit],
    virtual_record_id_to_result: dict[str, Any],
    ref_mapper: CitationRefMapper,
    *,
    is_multimodal_llm: bool,
    record_id_shortener: RecordIdShortener | None,
    max_chars: int,
) -> tuple[list[Unit], int]:
    """Records in rank order: whole if they fit, else their best units that fit.

    A record with no unit that fits is skipped rather than ending the walk, so
    one oversized record does not cost every smaller record ranked after it.
    """
    def measure(record_units: list[Unit]) -> int:
        # The real render overwrites the per-unit fields this one sets.
        content_array, _ = build_message_content_array(
            record_units,
            virtual_record_id_to_result,
            is_multimodal_llm=is_multimodal_llm,
            ref_mapper=copy.deepcopy(ref_mapper),
            from_tool=True,
            record_id_shortener=copy.deepcopy(record_id_shortener),
            image_budget=ImageBudget(),
        )
        return sum(len(_record_text(record)) + 1 for record in content_array)

    by_record: dict[Any, list[Unit]] = {}
    for unit in units:
        by_record.setdefault(unit.get("virtual_record_id"), []).append(unit)

    kept: list[Unit] = []
    omitted = 0
    used = 0
    for record_units in by_record.values():
        room = max_chars - used
        size = measure(record_units)
        if size > room:
            record_units, size = _best_units_within(
                record_units, room, measure, keep_one=not kept,
            )
        if not record_units:
            omitted += 1
            continue
        kept.extend(record_units)
        used += size
    return kept, omitted


def _best_units_within(
    units: list[Unit],
    room: int,
    measure: Callable[[list[Unit]], int],
    *,
    keep_one: bool,
) -> tuple[list[Unit], int]:
    """The record's most relevant units that render within ``room``, in reading order.

    Unscored units (neighbours) are tried last. ``keep_one`` keeps the best
    unit even when it alone is over, so the top record is never dropped.
    """
    position = {id(unit): i for i, unit in enumerate(units)}
    chosen: set[int] = set()
    size = 0
    for unit in RelevanceRanker().rank(units):
        trial = chosen | {position[id(unit)]}
        trial_size = measure([units[i] for i in sorted(trial)])
        if trial_size <= room or (keep_one and not chosen):
            chosen, size = trial, trial_size
    return [units[i] for i in sorted(chosen)], size


def _record_text(record: list[dict[str, Any]]) -> str:
    return "".join(item["text"] for item in record if item.get("type") == "text")
