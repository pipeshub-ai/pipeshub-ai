"""Rendering chosen units as the ``<record>`` text the model reads, within a size cap."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.context.ordering import order_for_reading
from app.modules.retrieval.context.ranking import rank_units
from app.modules.retrieval.context.units import (
    is_orphan_neighbour,
    without_orphan_neighbours,
)
from app.utils.chat_helpers import admission_for, build_message_content_array

if TYPE_CHECKING:
    from app.modules.retrieval.context.units import Unit
    from app.utils.chat_helpers import CitationRefMapper, ImageBudget, RecordIdShortener
    from app.utils.image_admission import ImageAdmission


@dataclass
class RenderedKnowledge:
    units: list[Unit]
    """The units shown, in reading order: what the model can cite."""
    ranked: list[Unit]
    """The same units, records in rank order, each record's best units first."""
    parts: list[list[dict[str, Any]]]
    """Content parts of each record shown, in reading order."""
    images: list[dict[str, Any]] = field(default_factory=list)
    """Images collected for multimodal delivery alongside the text."""
    omitted_records: int = 0
    """Records left out entirely to stay within ``max_chars``."""

    @property
    def records(self) -> list[str]:
        return [_record_text(parts) for parts in self.parts]

    @property
    def text(self) -> str:
        return "\n".join(self.records)


@dataclass(frozen=True)
class Selection:
    """What a render will show, decided before anything is rendered for real."""

    ranked: list[Unit]
    omitted_records: int = 0
    chars: int | None = None
    """Length of the text the render will produce; None when not measured."""
    record_id_shortener: RecordIdShortener | None = None
    """A copy of the short ids as the render will leave them, when measured,
    so text added after the records can be sized with the ids it will get."""


@dataclass
class _RenderState:
    """Everything a render advances: citation refs, short record ids, image slots."""

    ref_mapper: CitationRefMapper
    record_id_shortener: RecordIdShortener | None
    image_admission: ImageAdmission

    def fork(self) -> _RenderState:
        return _RenderState(
            ref_mapper=self.ref_mapper.copy(),
            record_id_shortener=(
                self.record_id_shortener.copy() if self.record_id_shortener is not None else None
            ),
            image_admission=self.image_admission.trial(),
        )


class KnowledgeRenderer:
    """Renders ranked units as ``<record>`` text on the request's shared state.

    ``select`` only measures; ``render`` assigns citation refs, short record
    ids and image slots. Nothing may change that state between the two, or
    the measured size no longer holds.
    """

    def __init__(
        self,
        virtual_record_id_to_result: dict[str, Any],
        *,
        ref_mapper: CitationRefMapper,
        is_multimodal_llm: bool,
        by_relevance: bool,
        record_id_shortener: RecordIdShortener | None = None,
        image_budget: ImageBudget | None = None,
        image_admission: ImageAdmission | None = None,
    ) -> None:
        self._records = virtual_record_id_to_result
        self._state = _RenderState(
            ref_mapper=ref_mapper,
            record_id_shortener=record_id_shortener,
            image_admission=admission_for(image_admission, image_budget),
        )
        self._renderer = _Renderer(
            virtual_record_id_to_result, is_multimodal_llm=is_multimodal_llm, by_relevance=by_relevance,
        )
        self._by_relevance = by_relevance

    def select(self, ranked: list[Unit], max_chars: int | None = None) -> Selection:
        """The units of ``ranked`` (most relevant first) to show.

        With ``max_chars`` the text fits within it: records are kept in rank
        order, whole when they fit, else cut to their most relevant units
        that fit, and a record with none that fit is left out. So an
        oversized result loses its least relevant content, not whatever a
        character cut lands on. Only the most relevant unit is kept
        regardless of size.
        """
        ranked = [
            unit for unit in ranked
            if self._records.get(unit.get("virtual_record_id")) is not None
        ]
        if max_chars is None:
            return Selection(ranked=ranked)
        return self._renderer.fit(ranked, self._state, max_chars)

    def render(self, selection: Selection) -> RenderedKnowledge:
        units = order_for_reading(selection.ranked, by_relevance=self._by_relevance)
        images: list[dict[str, Any]] = []
        parts = self._renderer.render(units, self._state, images)
        return RenderedKnowledge(
            units=units,
            ranked=selection.ranked,
            parts=parts,
            images=images,
            omitted_records=selection.omitted_records,
        )


def render_knowledge(
    ranked: list[Unit],
    virtual_record_id_to_result: dict[str, Any],
    *,
    ref_mapper: CitationRefMapper,
    is_multimodal_llm: bool,
    by_relevance: bool,
    record_id_shortener: RecordIdShortener | None = None,
    image_budget: ImageBudget | None = None,
    image_admission: ImageAdmission | None = None,
    max_chars: int | None = None,
) -> RenderedKnowledge:
    """Select within ``max_chars`` and render, in one step (see ``KnowledgeRenderer``)."""
    renderer = KnowledgeRenderer(
        virtual_record_id_to_result,
        ref_mapper=ref_mapper,
        is_multimodal_llm=is_multimodal_llm,
        by_relevance=by_relevance,
        record_id_shortener=record_id_shortener,
        image_budget=image_budget,
        image_admission=image_admission,
    )
    return renderer.render(renderer.select(ranked, max_chars))


class _Renderer:
    def __init__(
        self,
        virtual_record_id_to_result: dict[str, Any],
        *,
        is_multimodal_llm: bool,
        by_relevance: bool,
    ) -> None:
        self._records = virtual_record_id_to_result
        self._is_multimodal_llm = is_multimodal_llm
        self._by_relevance = by_relevance

    def render(
        self, units: list[Unit], state: _RenderState, images: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        content_array, _ = build_message_content_array(
            units,
            self._records,
            is_multimodal_llm=self._is_multimodal_llm,
            ref_mapper=state.ref_mapper,
            from_tool=True,
            record_id_shortener=state.record_id_shortener,
            collected_images=images,
            image_budget=state.image_admission.budget,
            image_admission=state.image_admission,
        )
        return content_array

    def measure(self, units: list[Unit], state: _RenderState) -> tuple[int, _RenderState]:
        """Characters ``units`` add to the text, one separator included, and
        the state the render leaves behind."""
        trial = state.fork()
        parts = self.render(order_for_reading(units, by_relevance=self._by_relevance), trial, [])
        return sum(len(_record_text(record)) + 1 for record in parts), trial

    def fit(self, ranked: list[Unit], state: _RenderState, max_chars: int) -> Selection:
        """Sizing renders on copies of ``state`` carried forward record by
        record, so refs, short ids and image slots are those of the real render."""
        if not ranked:
            return Selection(ranked=[], chars=0)
        whole, after = self.measure(ranked, state)
        if whole - 1 <= max_chars:
            return Selection(
                ranked=ranked, chars=max(whole - 1, 0), record_id_shortener=after.record_id_shortener,
            )

        by_record: dict[Any, list[Unit]] = {}
        for unit in ranked:
            by_record.setdefault(unit.get("virtual_record_id"), []).append(unit)

        kept: list[list[Unit]] = []
        omitted = 0
        used = 0
        probe = state
        for record_units in by_record.values():
            room = max_chars + 1 - used
            if kept and room <= 0:
                omitted += 1
                continue
            shown = record_units
            size, after = self.measure(shown, probe)
            if size > room:
                shown, size, after = self._best_units_within(
                    record_units, room, probe, keep_one=not kept,
                )
            if not shown:
                omitted += 1
                continue
            kept.append(shown)
            used += size
            probe = after
        dropped, chars, after = self._trim_to_fit(kept, state, max_chars)
        return Selection(
            ranked=[unit for record_units in kept for unit in record_units],
            omitted_records=omitted + dropped,
            chars=chars,
            record_id_shortener=after.record_id_shortener,
        )

    def _best_units_within(
        self, units: list[Unit], room: int, probe: _RenderState, *, keep_one: bool,
    ) -> tuple[list[Unit], int, _RenderState]:
        """The record's most relevant units that render within ``room``.

        Unscored units (neighbours, FK tables) are tried last, and a
        neighbour only beside a unit it belongs to. ``keep_one`` keeps the
        best unit even when it alone is over, so the most relevant record is
        never dropped.
        """
        chosen: list[Unit] = []
        size, after = 0, probe
        for unit in rank_units(units):
            if is_orphan_neighbour(unit, chosen):
                continue
            # A unit too big on its own is too big beside others: skip the
            # costlier render with them. The final check catches any slack.
            if chosen and self.measure([unit], probe)[0] > room:
                continue
            trial_size, trial_after = self.measure([*chosen, unit], probe)
            if trial_size <= room or (keep_one and not chosen):
                chosen.append(unit)
                size, after = trial_size, trial_after
        return chosen, size, after

    def _trim_to_fit(
        self, kept: list[list[Unit]], state: _RenderState, max_chars: int,
    ) -> tuple[int, int, _RenderState]:
        """Drop the least relevant units until the real render fits. Returns
        how many records went entirely, the length of the text left, and the
        state its render leaves behind.

        Record-by-record sizing is exact for refs and short ids, but the real
        render ranks images across the whole result and lays records out in
        reading order, so its text can still differ slightly.
        """
        dropped = 0
        while True:
            size, after = self.measure([unit for record_units in kept for unit in record_units], state)
            chars = max(size - 1, 0)
            if chars <= max_chars:
                break
            last = kept[-1]
            if len(last) > 1:
                kept[-1] = without_orphan_neighbours(rank_units(last)[:-1])
            elif len(kept) > 1:
                kept.pop()
                dropped += 1
            else:
                break
        return dropped, chars, after


def _record_text(record: list[dict[str, Any]]) -> str:
    return "".join(item["text"] for item in record if item.get("type") == "text")
