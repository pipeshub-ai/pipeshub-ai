"""Rendering ordered units as the ``<record>`` text the model reads."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.context.budget import fit_to_budget
from app.modules.retrieval.context.manifest import (
    ContentManifest,
    ManifestSource,
    build_manifest,
)
from app.modules.retrieval.context.ordering import order_for_reading
from app.utils.chat_helpers import (
    CitationRefMapper,
    ImageBudget,
    build_message_content_array,
)

if TYPE_CHECKING:
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
    omitted_hits: int = 0
    """Lower-ranked hits left out to stay within ``max_chars``."""
    manifest: ContentManifest = field(
        default_factory=lambda: ContentManifest(ManifestSource.SEARCH, (), ()),
    )
    """Which characters of ``text`` show which blocks."""

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
    source: ManifestSource = ManifestSource.SEARCH,
) -> RenderedKnowledge:
    """Render ``units`` (already in reading order) record by record.

    With ``max_chars`` the least relevant hits are left out, each with the
    neighbours around it, so an oversized result loses what matters least
    rather than whatever a later character cut happens to land on. The most
    relevant hit is always kept. Units are measured on a throwaway render
    first so that citation refs and images are only ever assigned to what is
    shown.
    """
    units = [
        unit for unit in units
        if virtual_record_id_to_result.get(unit.get("virtual_record_id")) is not None
    ]
    omitted = 0
    if max_chars is not None:
        trial = _render(
            units, virtual_record_id_to_result,
            ref_mapper=copy.deepcopy(ref_mapper),
            is_multimodal_llm=is_multimodal_llm,
            record_id_shortener=copy.deepcopy(record_id_shortener),
            image_budget=ImageBudget(),
            image_admission=None,
            source=source,
        )
        fit = fit_to_budget(units, trial.manifest, max_chars)
        units, omitted = order_for_reading(fit.units), fit.omitted_hits

    rendered = _render(
        units, virtual_record_id_to_result,
        ref_mapper=ref_mapper,
        is_multimodal_llm=is_multimodal_llm,
        record_id_shortener=record_id_shortener,
        image_budget=image_budget,
        image_admission=image_admission,
        source=source,
    )
    rendered.omitted_hits = omitted
    return rendered


def _render(
    units: list[Unit],
    virtual_record_id_to_result: dict[str, Any],
    *,
    ref_mapper: CitationRefMapper,
    is_multimodal_llm: bool,
    record_id_shortener: RecordIdShortener | None,
    image_budget: ImageBudget | None,
    image_admission: ImageAdmission | None,
    source: ManifestSource,
) -> RenderedKnowledge:
    images: list[dict[str, Any]] = []
    item_units: dict[int, int] = {}
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
        item_units=item_units,
    )
    return RenderedKnowledge(
        records=[_record_text(record) for record in content_array],
        units=units,
        images=images,
        manifest=build_manifest(
            source, content_array, units, item_units, virtual_record_id_to_result,
        ),
    )


def _record_text(record: list[dict[str, Any]]) -> str:
    return "".join(item["text"] for item in record if item.get("type") == "text")
