"""The section a hit sits in, shown inline with the hit."""

from __future__ import annotations

from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.renderer import render_knowledge
from app.utils.chat_helpers import CitationRefMapper, _attach_section_paths


def _record(vrid: str) -> dict:
    return {
        "id": f"id-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": f"Record ID: id-{vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [
                {"type": BlockType.TEXT.value, "data": "Parcels ship on Mondays.",
                 "citation_metadata": {"section_title": "Operations › Shipping"}},
                {"type": BlockType.TEXT.value, "data": "No section here."},
                {"type": BlockType.TABLE_ROW.value, "parent_index": 0,
                 "data": {"row_natural_language_text": "Zone: A, Price: 5"}},
            ],
            "block_groups": [
                {"type": GroupType.TABLE.value, "data": {"table_summary": "Rates"},
                 "citation_metadata": {"section_title": "Operations › Shipping › Rates"}},
            ],
        },
    }


def _text_unit(vrid: str, index: int, text: str) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value,
            "content": text, "metadata": {}}


def _render(units: list[dict], records: dict, **kwargs):
    _attach_section_paths(units, records)
    return render_knowledge(units, records, ref_mapper=CitationRefMapper(),
                            is_multimodal_llm=False, **kwargs)


def test_text_hit_shows_its_section_inline():
    records = {"a": _record("a")}
    rendered = _render([_text_unit("a", 0, "Parcels ship on Mondays.")], records)
    assert "[0|ref1] (§ Operations › Shipping) Parcels ship on Mondays." in rendered.text


def test_hit_without_a_section_renders_as_before():
    records = {"a": _record("a")}
    rendered = _render([_text_unit("a", 1, "No section here.")], records)
    assert "[1|ref1] No section here." in rendered.text
    assert "§" not in rendered.text


def test_section_note_adds_no_citation_ref():
    records = {"a": _record("a")}
    mapper = CitationRefMapper()
    units = [_text_unit("a", 0, "Parcels ship on Mondays.")]
    _attach_section_paths(units, records)
    render_knowledge(units, records, ref_mapper=mapper, is_multimodal_llm=False)
    assert len(mapper.ref_to_url) == 1


def test_table_shows_its_section_after_its_header():
    records = {"a": _record("a")}
    row = {"virtual_record_id": "a", "block_index": 2, "block_type": BlockType.TABLE_ROW.value,
           "content": "Zone: A, Price: 5", "metadata": {}}
    table = {"virtual_record_id": "a", "block_index": 2, "block_group_index": 0,
             "block_type": GroupType.TABLE.value, "content": ("Rates", [row]), "metadata": {}}
    rendered = _render([table], records)
    assert "[Table #0: Rates] (§ Operations › Shipping › Rates)\n[2|" in rendered.text


def test_render_with_section_notes_stays_within_max_chars():
    records = {v: _record(v) for v in ("a", "b", "c")}
    units = [_text_unit(v, 0, "Parcels ship on Mondays. " * 10) for v in ("a", "b", "c")]
    one = len(_render(units[:1], records).text)
    budget = one * 2 + 10

    rendered = _render(units, records, max_chars=budget)

    assert len(rendered.text) <= budget
    assert rendered.omitted_hits == 1
    assert rendered.text.count("(§ Operations › Shipping)") == 2
